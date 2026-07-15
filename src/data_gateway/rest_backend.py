import os
import uuid
import asyncio
import logging
import threading
from typing import Optional, Any
from datetime import datetime
from supabase import create_client, Client

logger = logging.getLogger(__name__)

class RestGateway:
    def __init__(self):
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_url or not supabase_key:
            raise ValueError("Supabase URL and Service Role Key must be configured for REST mode.")
        self._url = supabase_url
        self._key = supabase_key
        self._local = threading.local()

    @property
    def client(self) -> Client:
        """
        A Supabase client owned by the calling thread.

        Every method here runs its blocking call through asyncio.to_thread, so a
        single shared client means one httpx connection pool used from many
        worker threads at once. That is not safe: concurrent requests (the
        dashboard fires five at a time, and the chat pipeline gathers several)
        interleave on the same socket and fail with
        "WinError 10035 / non-blocking socket operation could not be completed".

        Giving each worker thread its own client gives each its own pool. The
        executor reuses a small, bounded set of threads, so this creates a
        handful of clients, not one per request.
        """
        client = getattr(self._local, "client", None)
        if client is None:
            client = create_client(self._url, self._key)
            self._local.client = client
        return client

    # ── User Operations ──
    async def get_user_by_id(self, user_id: str) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("users").select("*").eq("id", str(user_id)).execute)
        return res.data[0] if res.data else None

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("users").select("*").eq("email", email).execute)
        return res.data[0] if res.data else None

    async def create_user(self, user_data: dict) -> dict:
        # The Supabase users table has no server-side defaults for id/plan —
        # supply them client-side or the insert violates NOT NULL constraints.
        user_data = {
            "id": str(uuid.uuid4()),
            "is_admin": False,
            "plan": "free",
            **user_data,
        }
        res = await asyncio.to_thread(self.client.table("users").insert(user_data).execute)
        return res.data[0] if res.data else None

    async def get_all_users(self, limit: int = 50, offset: int = 0) -> list[dict]:
        res = await asyncio.to_thread(self.client.table("users").select("*").order("created_at", desc=True).limit(limit).offset(offset).execute)
        return res.data

    async def get_user_context_profile(self, user_id: str) -> dict:
        res = await asyncio.to_thread(self.client.table("user_context_profiles").select("*").eq("user_id", str(user_id)).execute)
        if res.data:
            profile = res.data[0]
            profile["id"] = str(user_id)  # Ensure 'id' is always present
            return profile
        # Return default if not exists
        return {"id": str(user_id), "context_text": "", "preferred_language": "english", "llm_mode": "cloud"}

    async def update_user_context_profile(self, user_id: str, data: dict) -> dict:
        data["user_id"] = str(user_id)
        res = await asyncio.to_thread(self.client.table("user_context_profiles").upsert(data, on_conflict="user_id").execute)
        return res.data[0] if res.data else data

    # ── Session & Message Operations ──
    async def create_session(self, session_id: str, user_id: str, title: str, project_id: Optional[str] = None) -> None:
        data = {"session_id": str(session_id), "user_id": str(user_id) if user_id else None, "title": title}
        if project_id:
            data["project_id"] = project_id
        await asyncio.to_thread(self.client.table("sessions").insert(data).execute)

    async def get_sessions_for_user(self, user_id: str, project_id: Optional[str] = None) -> list[dict]:
        query = self.client.table("sessions").select("*").eq("user_id", str(user_id)).is_("deleted_at", "null")
        if project_id:
            query = query.eq("project_id", project_id)
        # In global view, we want to return all sessions for the user, regardless of project_id.
        # So we do not apply any project_id filter if project_id is None.
        res = await asyncio.to_thread(query.order("updated_at", desc=True).execute)
        return res.data

    async def get_session(self, session_id: str) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("sessions").select("*").eq("session_id", str(session_id)).is_("deleted_at", "null").execute)
        return res.data[0] if res.data else None

    async def update_session_title(self, session_id: str, title: str) -> None:
        await asyncio.to_thread(self.client.table("sessions").update({"title": title, "updated_at": datetime.utcnow().isoformat()}).eq("session_id", str(session_id)).execute)

    async def delete_session(self, session_id: str) -> None:
        await asyncio.to_thread(self.client.table("sessions").update({"deleted_at": datetime.utcnow().isoformat()}).eq("session_id", str(session_id)).execute)

    async def save_message(self, session_id: str, role: str, content: str) -> None:
        # message_id has no server-side default in the Supabase schema —
        # omitting it violates the NOT NULL constraint and the message is lost.
        data = {"message_id": str(uuid.uuid4()), "session_id": str(session_id), "role": role, "content": content}
        await asyncio.to_thread(self.client.table("messages").insert(data).execute)

    async def get_session_history(self, session_id: str) -> list[dict]:
        res = await asyncio.to_thread(self.client.table("messages").select("*").eq("session_id", str(session_id)).order("created_at").execute)
        return res.data

    async def update_message_citations(self, session_id: str, response_text: str, unverified: list[str]) -> None:
        # Find the most recent assistant message with this exact content.
        # The PK column is message_id (selecting "id" errors on this table).
        res = await asyncio.to_thread(
            self.client.table("messages")
            .select("message_id")
            .eq("session_id", str(session_id))
            .eq("role", "assistant")
            .eq("content", response_text)
            .order("created_at", desc=True)
            .limit(1)
            .execute
        )
        if res.data:
            msg_id = res.data[0]["message_id"]
            await asyncio.to_thread(
                self.client.table("messages")
                .update({"citation_validated": True, "unverified_citations": unverified})
                .eq("message_id", msg_id)
                .execute
            )

    # ── Pipeline & Admin Operations ──
    async def create_run(self, session_id: str, user_message: str) -> str:
        import uuid
        run_id = str(uuid.uuid4())
        data = {"run_id": run_id, "session_id": str(session_id), "original_query": user_message, "retry_count": 0}
        await asyncio.to_thread(self.client.table("pipeline_runs").insert(data).execute)
        return run_id

    async def update_run(self, run_id: str, **kwargs) -> None:
        allowed = {"rewritten_query", "routed_to", "retry_count", "final_outcome", "total_duration_ms"}
        data = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if data:
            await asyncio.to_thread(self.client.table("pipeline_runs").update(data).eq("run_id", str(run_id)).execute)

    # SSE event statuses → Postgres CHECK constraint vocabulary
    _STEP_STATUS_MAP = {"done": "success", "active": "success", "error": "failed", "retry": "retry", "skipped": "skipped"}

    async def log_step(self, run_id: str, step_name: str, step_order: int, status: str,
                       duration_ms: int = None, input_summary: dict = None, output_summary: dict = None) -> None:
        if not run_id:
            return
        data = {
            "run_id": str(run_id), "step_name": step_name, "step_order": step_order,
            "status": self._STEP_STATUS_MAP.get(status, status), "duration_ms": duration_ms,
            "input_summary": input_summary, "output_summary": output_summary,
        }
        await asyncio.to_thread(self.client.table("pipeline_steps").insert(data).execute)

    async def create_step(self, run_id: str, step_name: str, step_order: int) -> int:
        data = {"run_id": str(run_id), "step_name": step_name, "step_order": step_order, "status": "running"}
        res = await asyncio.to_thread(self.client.table("pipeline_steps").insert(data).execute)
        return res.data[0]["step_id"] if res.data else None

    async def update_step(self, step_id: int, **kwargs) -> None:
        if not kwargs: return
        await asyncio.to_thread(self.client.table("pipeline_steps").update(kwargs).eq("step_id", step_id).execute)

    async def log_mcp_tool_call(self, run_id: str, mcp_server: str, tool_name: str, status: str, input_params: dict = None, output_summary: dict = None, duration_ms: int = None, rejected_by_role: bool = False) -> None:
        data = {
            "call_id": str(uuid.uuid4()),
            "run_id": str(run_id),
            "mcp_server": mcp_server,
            "tool_name": tool_name,
            "status": status,
            "input_params": input_params,
            "output_summary": output_summary,
            "duration_ms": duration_ms,
            "rejected_by_role": rejected_by_role
        }
        await asyncio.to_thread(self.client.table("mcp_tool_calls").insert(data).execute)

    async def log_document(self, doc_id: str, filename: str, doc_type: str = None, chunk_count: int = None, is_global: bool = False) -> None:
        data = {"doc_id": doc_id, "filename": filename, "doc_type": doc_type, "chunk_count": chunk_count, "is_global": is_global}
        # filter out None values to not overwrite with null accidentally during upsert, except where it matters
        data = {k: v for k, v in data.items() if v is not None}
        await asyncio.to_thread(self.client.table("documents").upsert(data, on_conflict="doc_id").execute)

    async def log_generated_file(self, file_data: dict) -> str:
        # file_id has no server-side default — generate it client-side.
        file_data = {**file_data, "file_id": file_data.get("file_id") or str(uuid.uuid4())}
        res = await asyncio.to_thread(self.client.table("generated_files").insert(file_data).execute)
        return res.data[0]["file_id"] if res.data else None

    async def get_generated_file(self, file_id: str) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("generated_files").select("*").eq("file_id", str(file_id)).execute)
        return res.data[0] if res.data else None

    async def get_system_metrics(self) -> dict:
        try:
            # Fetch data using REST
            runs_res = await asyncio.to_thread(self.client.table("pipeline_runs").select("run_id,routed_to,final_outcome,total_duration_ms").execute)
            users_res = await asyncio.to_thread(self.client.table("users").select("id", count="exact", head=True).execute)
            sessions_res = await asyncio.to_thread(self.client.table("sessions").select("session_id", count="exact", head=True).execute)
            files_res = await asyncio.to_thread(self.client.table("generated_files").select("file_id,file_type,file_size_bytes").execute)
            chunks_res = await asyncio.to_thread(self.client.table("document_chunks").select("chunk_id", count="exact", head=True).execute)
            docs_res = await asyncio.to_thread(self.client.table("documents").select("doc_id", count="exact", head=True).execute)
            mcp_res = await asyncio.to_thread(self.client.table("mcp_tool_calls").select("call_id", count="exact", head=True).execute)

            runs = runs_res.data or []
            files = files_res.data or []

            total_runs = len(runs)
            total_duration = sum(r.get("total_duration_ms") or 0 for r in runs)
            avg_duration = int(total_duration / total_runs) if total_runs > 0 else 0

            # Route metrics with success rates
            routes = {}
            for r in runs:
                route = r.get("routed_to") or "unknown"
                outcome = r.get("final_outcome")

                if route not in routes:
                    routes[route] = {"count": 0, "success": 0}

                routes[route]["count"] += 1
                # Success = run completed with a real answer (not crashed, not the "safe" fallback)
                if outcome is not None and outcome != "safe":
                    routes[route]["success"] += 1

            # Format routes for frontend (return total count and success rate %)
            route_metrics = {}
            for route, data in routes.items():
                rate = (data["success"] / data["count"]) * 100 if data["count"] > 0 else 0
                # Using a structured object so frontend can read volume and success rate
                route_metrics[route] = {"count": data["count"], "success_rate": round(rate, 1)}

            # Fallback format for existing simple volume chart if it expects just numbers
            # We provide both.
            routes_flat = {k: v["count"] for k, v in route_metrics.items()}

            file_types = {}
            total_storage = 0
            for f in files:
                ftype = f.get("file_type") or "unknown"
                size = f.get("file_size_bytes") or 0
                file_types[ftype] = file_types.get(ftype, 0) + 1
                total_storage += size

            table_stats = [
                {"table": "document_chunks", "count": chunks_res.count or 0},
                {"table": "documents", "count": docs_res.count or 0},
                {"table": "pipeline_runs", "count": total_runs},
                {"table": "sessions", "count": sessions_res.count or 0},
                {"table": "users", "count": users_res.count or 0},
                {"table": "mcp_tool_calls", "count": mcp_res.count or 0},
                {"table": "generated_files", "count": len(files)},
            ]

            return {
                "total_runs": total_runs,
                "total_users": users_res.count or 0,
                "total_sessions": sessions_res.count or 0,
                "total_files": len(files),
                "total_storage_bytes": total_storage,
                "routes": routes_flat,
                "route_metrics": route_metrics,
                "file_types": file_types,
                "avg_duration_ms": avg_duration,
                "table_stats": table_stats
            }
        except Exception as e:
            logger.error(f"Error fetching REST metrics: {e}")
            return {"error": str(e)}

    async def get_runs(self, limit: int = 50, offset: int = 0, route_filter: str = None) -> list[dict]:
        query = self.client.table("pipeline_runs").select("*").order("created_at", desc=True).limit(limit).offset(offset)
        if route_filter:
            query = query.eq("routed_to", route_filter)
        res = await asyncio.to_thread(query.execute)
        return res.data

    async def get_run_steps(self, run_id: str) -> list[dict]:
        res = await asyncio.to_thread(self.client.table("pipeline_steps").select("*").eq("run_id", str(run_id)).order("step_order").execute)
        return res.data

    async def get_generated_files(self, limit: int = 50, offset: int = 0) -> list[dict]:
        res = await asyncio.to_thread(self.client.table("generated_files").select("*").order("created_at", desc=True).limit(limit).offset(offset).execute)
        return res.data

    async def delete_generated_file(self, file_id: str) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("generated_files").delete().eq("file_id", str(file_id)).execute)
        return res.data[0] if res.data else None

    async def get_mcp_calls(self, limit: int = 50, offset: int = 0) -> list[dict]:
        # Column names must match the real mcp_tool_calls table: it has
        # created_at / duration_ms / output_summary, NOT started_at /
        # completed_at / error_message (those never existed and made this 500).
        res = await asyncio.to_thread(
            self.client.table("mcp_tool_calls")
            .select("call_id,run_id,mcp_server,tool_name,input_params,output_summary,status,duration_ms,created_at,run:pipeline_runs(original_query)")
            .order("created_at", desc=True)
            .limit(limit)
            .offset(offset)
            .execute
        )
        return res.data

    async def get_ingested_files_summary(self, project_id: Optional[str] = None) -> list[dict]:
        query = self.client.table("documents").select("*")
        if project_id:
            query = query.eq("project_id", project_id)
        res = await asyncio.to_thread(query.execute)
        return res.data

    async def delete_ingested_file(self, doc_id: str) -> None:
        await asyncio.to_thread(self.client.table("documents").delete().eq("doc_id", str(doc_id)).execute)

    # ── Project Operations ──
    async def get_project(self, project_id: str) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("projects").select("*").eq("id", str(project_id)).execute)
        return res.data[0] if res.data else None

    async def get_projects_for_user(self, user_id: str) -> list[dict]:
        res = await asyncio.to_thread(self.client.table("projects").select("*").eq("user_id", str(user_id)).order("created_at", desc=True).execute)
        return res.data

    async def create_project(self, data: dict) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("projects").insert(data).execute)
        return res.data[0] if res.data else None

    async def update_project(self, project_id: str, data: dict) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("projects").update(data).eq("id", str(project_id)).execute)
        return res.data[0] if res.data else None

    async def delete_project(self, project_id: str) -> None:
        await asyncio.to_thread(self.client.table("projects").delete().eq("id", str(project_id)).execute)

    async def get_project_context(self, project_id: str) -> Optional[str]:
        res = await asyncio.to_thread(self.client.table("projects").select("domain_context").eq("id", str(project_id)).limit(1).execute)
        return res.data[0].get("domain_context") if res.data else None

    async def get_project_memory(self, project_id: str) -> Optional[dict]:
        res = await asyncio.to_thread(self.client.table("project_memory").select("*").eq("project_id", str(project_id)).limit(1).execute)
        return res.data[0] if res.data else None

    async def upsert_project_memory(self, project_id: str, summary_text: str) -> None:
        existing = await self.get_project_memory(project_id)
        if existing:
            await asyncio.to_thread(self.client.table("project_memory").update({"summary_text": summary_text}).eq("id", existing["id"]).execute)
        else:
            await asyncio.to_thread(self.client.table("project_memory").insert({"project_id": str(project_id), "summary_text": summary_text}).execute)

    async def delete_document_records(self, source_file: str) -> None:
        await asyncio.to_thread(self.client.table("documents").delete().eq("filename", source_file).execute)

    # ── Vector Store Operations ──
    async def insert_documents(self, documents: list[dict]) -> None:
        if not documents: return
        await asyncio.to_thread(self.client.table("documents").upsert(documents, on_conflict="doc_id").execute)

    async def insert_chunks(self, chunks: list[dict]) -> None:
        if not chunks: return
        batch_size = 100
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            await asyncio.to_thread(self.client.table("document_chunks").upsert(batch, on_conflict="chunk_id").execute)

    async def delete_chunks_by_source(self, source: str) -> int:
        res = await asyncio.to_thread(self.client.table("document_chunks").delete().eq("source_file", source).execute)
        return len(res.data)

    async def query_similar_chunks(self, query_text: str, query_embedding: list[float], top_k: int = 10, where: Optional[dict] = None) -> list[dict]:
        params = {
            "query_embedding": list(query_embedding),
            "match_count": top_k
        }
        if where and "project_id" in where:
            params["filter_project_id"] = where["project_id"]

        res = await asyncio.to_thread(self.client.rpc("match_documents", params).execute)
        # Normalize the RPC's row shape (chunk_id/chunk_text/source_file/similarity)
        # to the canonical shape the pipeline expects ({id, text, metadata, rrf_score}).
        # Without this, BM25/reranker/evaluator crash on KeyError('text') in REST mode.
        results = []
        for row in res.data or []:
            results.append({
                "id": row.get("chunk_id") or row.get("id"),
                "text": row.get("chunk_text") or row.get("text") or "",
                "metadata": {"source": row.get("source_file") or "unknown"},
                "rrf_score": float(row.get("similarity") or row.get("rrf_score") or 0.0),
            })
        return results

    async def get_collection_count(self) -> int:
        res = await asyncio.to_thread(self.client.table("document_chunks").select("chunk_id", count="exact").execute)
        return res.count if res.count is not None else 0

    # ── Tools Operations ──
    async def query_tax_rates(self) -> str:
        # Simplified placeholder for Tax Rates tool
        return "REST query_tax_rates not fully implemented."

    # ══════════════════════════════════════════════════════════════════
    # Admin dashboard: errors, ingestion jobs, KB stats, usage, latency
    #
    # These degrade to empty datasets when migration 003 has not been
    # applied yet, so the dashboard renders a "not instrumented" notice
    # instead of a wall of 500s.
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _missing_table(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "does not exist" in msg or "could not find the table" in msg or "pgrst205" in msg

    async def table_exists(self, table: str) -> bool:
        """
        Does this table/view actually exist?

        Needed because every read path here swallows a missing table and returns
        an empty list — which is right for the charts, but means "no rows" and
        "no table" look identical. The dashboard has to tell those apart, or an
        un-run migration masquerades as a healthy, silent system.
        """
        try:
            await asyncio.to_thread(self.client.table(table).select("*").limit(1).execute)
            return True
        except Exception as exc:
            if self._missing_table(exc):
                return False
            raise

    async def log_error(self, record: dict) -> None:
        data = {"error_id": str(uuid.uuid4()), **record}
        try:
            await asyncio.to_thread(self.client.table("error_logs").insert(data).execute)
        except Exception:
            pass  # error logging must never raise

    async def get_errors(self, limit: int = 100, offset: int = 0, severity: str = None,
                         module: str = None, error_type: str = None,
                         since: str = None) -> list[dict]:
        try:
            q = self.client.table("error_logs").select("*")
            if severity:
                q = q.eq("severity", severity)
            if module:
                q = q.eq("module", module)
            if error_type:
                q = q.eq("error_type", error_type)
            if since:
                q = q.gte("occurred_at", since)
            res = await asyncio.to_thread(
                q.order("occurred_at", desc=True).limit(limit).offset(offset).execute
            )
            return res.data or []
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    async def get_error_facets(self) -> dict:
        """Distinct modules / types / severities, for the filter dropdowns."""
        try:
            res = await asyncio.to_thread(
                self.client.table("error_logs").select("module,error_type,severity")
                .limit(2000).execute
            )
            rows = res.data or []
            return {
                "modules": sorted({r["module"] for r in rows if r.get("module")}),
                "error_types": sorted({r["error_type"] for r in rows if r.get("error_type")}),
                "severities": sorted({r["severity"] for r in rows if r.get("severity")}),
            }
        except Exception as exc:
            if self._missing_table(exc):
                return {"modules": [], "error_types": [], "severities": []}
            raise

    async def get_errors_since(self, since: str) -> list[dict]:
        """Raw rows for trend bucketing (timestamps + severity only)."""
        try:
            res = await asyncio.to_thread(
                self.client.table("error_logs").select("occurred_at,severity")
                .gte("occurred_at", since).limit(5000).execute
            )
            return res.data or []
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    # ── Ingestion jobs ──
    async def create_ingestion_job(self, data: dict) -> str:
        job_id = str(uuid.uuid4())
        try:
            await asyncio.to_thread(
                self.client.table("ingestion_jobs").insert({"job_id": job_id, **data}).execute
            )
        except Exception as exc:
            if not self._missing_table(exc):
                raise
        return job_id

    async def update_ingestion_job(self, job_id: str, data: dict) -> None:
        try:
            await asyncio.to_thread(
                self.client.table("ingestion_jobs").update(data).eq("job_id", str(job_id)).execute
            )
        except Exception as exc:
            if not self._missing_table(exc):
                raise

    async def get_ingestion_jobs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        try:
            res = await asyncio.to_thread(
                self.client.table("ingestion_jobs").select("*")
                .order("started_at", desc=True).limit(limit).offset(offset).execute
            )
            return res.data or []
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    # ── Knowledge base stats ──
    async def get_kb_stats(self) -> dict:
        """
        Chunks indexed, and how they are distributed across documents.

        Reads the `knowledge_base_documents` view (migration 003), which groups
        chunks by source_file — the document identity a human recognises and the
        one retrieval cites. It deliberately does NOT trust documents.chunk_count:
        a chunker bug wrote every chunk under its own synthetic doc_id, so that
        column is null and the documents table holds one row per chunk for the
        existing corpus.
        """
        chunks = await asyncio.to_thread(
            self.client.table("document_chunks").select("chunk_id", count="exact", head=True).execute
        )
        total_chunks = chunks.count or 0

        try:
            res = await asyncio.to_thread(
                self.client.table("knowledge_base_documents").select("*")
                .order("chunk_count", desc=True).limit(500).execute
            )
            docs = [
                {
                    "doc_id": d.get("filename"),
                    "filename": d.get("filename"),
                    "doc_type": d.get("doc_type"),
                    "chunk_count": d.get("chunk_count") or 0,
                    "is_global": d.get("is_global", False),
                    "ingested_at": d.get("ingested_at"),
                }
                for d in (res.data or [])
            ]
            return {
                "total_chunks": total_chunks,
                "total_documents": len(docs),
                "documents": docs,
                "grouped_by": "source_file",
            }
        except Exception as exc:
            if not self._missing_table(exc):
                raise

        # View not created yet — fall back to the documents table and say so,
        # rather than quietly serving a chunk count of zero for everything.
        res = await asyncio.to_thread(
            self.client.table("documents")
            .select("doc_id,filename,doc_type,chunk_count,is_global,ingested_at")
            .limit(500).execute
        )
        docs = sorted(
            [
                {
                    "doc_id": d.get("doc_id"),
                    "filename": d.get("filename"),
                    "doc_type": d.get("doc_type"),
                    "chunk_count": d.get("chunk_count") or 0,
                    "is_global": d.get("is_global", False),
                    "ingested_at": d.get("ingested_at"),
                }
                for d in (res.data or [])
            ],
            key=lambda d: d["chunk_count"],
            reverse=True,
        )
        return {
            "total_chunks": total_chunks,
            "total_documents": len(docs),
            "documents": docs,
            "grouped_by": "doc_id",
            "degraded": "Run migration 003 for an accurate chunks-per-document breakdown.",
        }

    # ── Usage / routing / latency ──
    async def get_runs_since(self, since: str) -> list[dict]:
        res = await asyncio.to_thread(
            self.client.table("pipeline_runs")
            .select("run_id,routed_to,final_outcome,total_duration_ms,created_at,user_id")
            .gte("created_at", since).limit(10000).execute
        )
        return res.data or []

    async def get_step_latencies_since(self, since: str) -> list[dict]:
        res = await asyncio.to_thread(
            self.client.table("pipeline_steps").select("step_name,duration_ms,status,created_at")
            .gte("created_at", since).limit(20000).execute
        )
        return res.data or []

    # ── Session attachments (per-conversation, NOT the knowledge base) ──
    async def create_attachment(self, data: dict) -> dict:
        row = {"attachment_id": str(uuid.uuid4()), **data}
        res = await asyncio.to_thread(self.client.table("session_attachments").insert(row).execute)
        return res.data[0] if res.data else row

    async def get_attachments_for_session(self, session_id: str, include_text: bool = False) -> list[dict]:
        cols = "*" if include_text else "attachment_id,session_id,user_id,filename,file_type,file_size_bytes,char_count,status,error_message,created_at"
        try:
            res = await asyncio.to_thread(
                self.client.table("session_attachments").select(cols)
                .eq("session_id", str(session_id)).order("created_at").execute
            )
            return res.data or []
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    async def get_attachment(self, attachment_id: str) -> Optional[dict]:
        res = await asyncio.to_thread(
            self.client.table("session_attachments").select("*")
            .eq("attachment_id", str(attachment_id)).execute
        )
        return res.data[0] if res.data else None

    async def delete_attachment(self, attachment_id: str) -> None:
        await asyncio.to_thread(
            self.client.table("session_attachments").delete()
            .eq("attachment_id", str(attachment_id)).execute
        )
