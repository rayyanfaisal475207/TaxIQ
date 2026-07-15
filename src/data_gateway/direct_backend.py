import logging
import uuid
from typing import Optional, Any
from datetime import datetime

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.postgres import get_session, engine
from src.database.models import (
    User, UserContextProfile, Session, Message, PipelineRun, PipelineStep,
    GeneratedFile, Document, DocumentChunk, McpToolCall, Project, ProjectMemory
)

logger = logging.getLogger(__name__)

class DirectGateway:
    # ── User Operations ──
    async def get_user_by_id(self, user_id: str) -> Optional[dict]:
        async with get_session() as db:
            res = await db.execute(select(User).where(User.id == uuid.UUID(str(user_id))))
            u = res.scalars().first()
            return {"id": str(u.id), "email": u.email, "password_hash": u.password_hash, "is_admin": u.is_admin, "company_name": u.company_name, "plan": u.plan} if u else None

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        async with get_session() as db:
            res = await db.execute(select(User).where(User.email == email))
            u = res.scalars().first()
            return {"id": str(u.id), "email": u.email, "password_hash": u.password_hash, "is_admin": u.is_admin, "company_name": u.company_name, "plan": u.plan} if u else None

    async def create_user(self, user_data: dict) -> dict:
        async with get_session() as db:
            u = User(**user_data)
            db.add(u)
            await db.commit()
            await db.refresh(u)
            return {"id": str(u.id), "email": u.email, "password_hash": u.password_hash, "is_admin": u.is_admin, "company_name": u.company_name, "plan": u.plan}

    async def get_all_users(self, limit: int = 50, offset: int = 0) -> list[dict]:
        async with get_session() as db:
            res = await db.execute(select(User).order_by(desc(User.created_at)).limit(limit).offset(offset))
            return [{"id": str(u.id), "email": u.email, "is_admin": u.is_admin, "company_name": u.company_name, "plan": u.plan, "created_at": u.created_at.isoformat() if u.created_at else None} for u in res.scalars().all()]

    async def get_user_context_profile(self, user_id: str) -> dict:
        async with get_session() as db:
            res = await db.execute(select(UserContextProfile).where(UserContextProfile.user_id == uuid.UUID(str(user_id))))
            p = res.scalars().first()
            if p:
                return {"id": str(user_id), "context_text": p.context_text, "preferred_language": p.preferred_language, "llm_mode": p.llm_mode}
            return {"id": str(user_id), "context_text": "", "preferred_language": "english", "llm_mode": "cloud"}

    async def update_user_context_profile(self, user_id: str, data: dict) -> dict:
        async with get_session() as db:
            res = await db.execute(select(UserContextProfile).where(UserContextProfile.user_id == uuid.UUID(str(user_id))))
            p = res.scalars().first()
            if not p:
                p = UserContextProfile(user_id=uuid.UUID(str(user_id)), **data)
                db.add(p)
            else:
                for k, v in data.items():
                    setattr(p, k, v)
            await db.commit()
            return {"context_text": p.context_text, "preferred_language": p.preferred_language, "llm_mode": p.llm_mode}

    # ── Session & Message Operations ──
    async def create_session(self, session_id: str, user_id: str, title: str, project_id: Optional[str] = None) -> None:
        async with get_session() as db:
            session_kwargs = {
                "session_id": uuid.UUID(str(session_id)),
                "user_id": uuid.UUID(str(user_id)) if user_id else None,
                "title": title
            }
            if project_id:
                session_kwargs["project_id"] = uuid.UUID(str(project_id))
            db.add(Session(**session_kwargs))
            await db.commit()

    async def get_sessions_for_user(self, user_id: str, project_id: str | None = None) -> list[dict]:
        async with get_session() as db:
            q = select(Session).where(Session.user_id == uuid.UUID(str(user_id))).where(Session.deleted_at == None)
            if project_id:
                q = q.where(Session.project_id == uuid.UUID(str(project_id)))
            res = await db.execute(q.order_by(desc(Session.updated_at)))
            return [{"session_id": str(s.session_id), "title": s.title, "project_id": str(s.project_id) if s.project_id else None, "updated_at": s.updated_at.isoformat() if s.updated_at else None} for s in res.scalars().all()]

    async def get_session(self, session_id: str) -> Optional[dict]:
        async with get_session() as db:
            res = await db.execute(select(Session).where(Session.session_id == uuid.UUID(str(session_id))).where(Session.deleted_at == None))
            s = res.scalars().first()
            return {"session_id": str(s.session_id), "user_id": str(s.user_id) if s.user_id else None, "project_id": str(s.project_id) if s.project_id else None, "title": s.title} if s else None

    async def update_session_title(self, session_id: str, title: str) -> None:
        async with get_session() as db:
            res = await db.execute(select(Session).where(Session.session_id == uuid.UUID(str(session_id))))
            s = res.scalars().first()
            if s:
                s.title = title
                s.updated_at = datetime.utcnow()
                await db.commit()

    async def delete_session(self, session_id: str) -> None:
        async with get_session() as db:
            res = await db.execute(select(Session).where(Session.session_id == uuid.UUID(str(session_id))))
            s = res.scalars().first()
            if s:
                s.deleted_at = datetime.utcnow()
                await db.commit()

    async def save_message(self, session_id: str, role: str, content: str) -> None:
        async with get_session() as db:
            db.add(Message(session_id=uuid.UUID(str(session_id)), role=role, content=content))
            await db.commit()

    async def get_session_history(self, session_id: str) -> list[dict]:
        async with get_session() as db:
            res = await db.execute(select(Message).where(Message.session_id == uuid.UUID(str(session_id))).order_by(Message.created_at))
            return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None} for m in res.scalars().all()]

    async def update_message_citations(self, session_id: str, response_text: str, unverified: list[str]) -> None:
        async with get_session() as db:
            stmt = select(Message).where(Message.session_id == uuid.UUID(str(session_id)), Message.role == "assistant", Message.content == response_text).order_by(desc(Message.created_at)).limit(1)
            res = await db.execute(stmt)
            msg = res.scalars().first()
            if msg:
                msg.citation_validated = True
                msg.unverified_citations = unverified
                await db.commit()

    # ── Pipeline & Admin Operations ──
    async def create_run(self, session_id: str, user_message: str) -> str:
        async with get_session() as db:
            r = PipelineRun(session_id=uuid.UUID(str(session_id)), original_query=user_message)
            db.add(r)
            await db.commit()
            return str(r.run_id)

    async def update_run(self, run_id: str, **kwargs) -> None:
        if not run_id: return
        allowed = {"rewritten_query", "routed_to", "retry_count", "final_outcome", "total_duration_ms"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates: return
        async with get_session() as db:
            res = await db.execute(select(PipelineRun).where(PipelineRun.run_id == uuid.UUID(str(run_id))))
            r = res.scalars().first()
            if r:
                for k, v in updates.items(): setattr(r, k, v)
                await db.commit()

    # SSE event statuses → Postgres CHECK constraint vocabulary
    _STEP_STATUS_MAP = {"done": "success", "active": "success", "error": "failed", "retry": "retry", "skipped": "skipped"}

    async def log_step(self, run_id: str, step_name: str, step_order: int, status: str,
                       duration_ms: int = None, input_summary: dict = None, output_summary: dict = None) -> None:
        if not run_id:
            return
        async with get_session() as db:
            db.add(PipelineStep(
                run_id=uuid.UUID(str(run_id)), step_name=step_name, step_order=step_order,
                status=self._STEP_STATUS_MAP.get(status, status), duration_ms=duration_ms,
                input_summary=input_summary, output_summary=output_summary,
            ))
            await db.commit()

    async def create_step(self, run_id: str, step_name: str, step_order: int) -> int:
        async with get_session() as db:
            s = PipelineStep(run_id=uuid.UUID(str(run_id)), step_name=step_name, step_order=step_order, status="running")
            db.add(s)
            await db.commit()
            await db.refresh(s)
            return s.step_id

    async def update_step(self, step_id: int, **kwargs) -> None:
        allowed = {"status", "duration_ms", "input_summary", "output_summary"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates: return
        async with get_session() as db:
            res = await db.execute(select(PipelineStep).where(PipelineStep.step_id == step_id))
            s = res.scalars().first()
            if s:
                for k, v in updates.items(): setattr(s, k, v)
                await db.commit()

    async def log_mcp_tool_call(self, run_id: str, mcp_server: str, tool_name: str, status: str, input_params: dict = None, output_summary: dict = None, duration_ms: int = None, rejected_by_role: bool = False) -> None:
        async with get_session() as db:
            tc = McpToolCall(
                run_id=uuid.UUID(str(run_id)), mcp_server=mcp_server, tool_name=tool_name, 
                status=status, input_params=input_params, output_summary=output_summary, 
                duration_ms=duration_ms, rejected_by_role=rejected_by_role
            )
            db.add(tc)
            await db.commit()

    async def log_document(self, doc_id: str, filename: str, doc_type: str = None, chunk_count: int = None, is_global: bool = False) -> None:
        async with get_session() as db:
            res = await db.execute(select(Document).where(Document.doc_id == doc_id))
            d = res.scalars().first()
            if d:
                d.filename = filename
                if doc_type is not None: d.doc_type = doc_type
                if chunk_count is not None: d.chunk_count = chunk_count
                d.is_global = is_global
            else:
                db.add(Document(doc_id=doc_id, filename=filename, doc_type=doc_type, chunk_count=chunk_count, is_global=is_global))
            await db.commit()

    async def log_generated_file(self, file_data: dict) -> str:
        # File data will have session_id, user_id, file_type, file_name, file_size_bytes, storage_path
        async with get_session() as db:
            user_id_val = uuid.UUID(file_data["user_id"]) if file_data.get("user_id") else None
            gf = GeneratedFile(
                file_id=uuid.uuid4(),
                session_id=uuid.UUID(file_data["session_id"]),
                user_id=user_id_val,
                file_type=file_data["file_type"],
                file_name=file_data["file_name"],
                file_size_bytes=file_data["file_size_bytes"],
                storage_path=file_data["storage_path"]
            )
            db.add(gf)
            await db.commit()
            return str(gf.file_id)

    async def get_generated_file(self, file_id: str) -> Optional[dict]:
        async with get_session() as db:
            res = await db.execute(select(GeneratedFile).where(GeneratedFile.file_id == uuid.UUID(str(file_id))))
            gf = res.scalars().first()
            if gf:
                return {
                    "file_id": str(gf.file_id),
                    "session_id": str(gf.session_id),
                    "user_id": str(gf.user_id),
                    "file_type": gf.file_type,
                    "file_name": gf.file_name,
                    "file_size_bytes": gf.file_size_bytes,
                    "storage_path": gf.storage_path,
                    "created_at": gf.created_at.isoformat() if gf.created_at else None
                }
            return None

    async def get_system_metrics(self) -> dict:
        async with get_session() as db:
            total_runs = (await db.execute(select(func.count(PipelineRun.run_id)))).scalar() or 0
            routes_res = await db.execute(
                select(PipelineRun.routed_to, func.count(PipelineRun.run_id),
                       func.count(PipelineRun.run_id).filter(PipelineRun.final_outcome.isnot(None), PipelineRun.final_outcome != "safe"))
                .group_by(PipelineRun.routed_to)
            )
            routes = {}
            route_metrics = {}
            for route, count, success in routes_res.all():
                key = route or "unknown"
                routes[key] = count
                rate = (success / count) * 100 if count else 0
                route_metrics[key] = {"count": count, "success_rate": round(rate, 1)}
            avg_duration = int((await db.execute(select(func.avg(PipelineRun.total_duration_ms)))).scalar() or 0)
            files_type_res = await db.execute(select(GeneratedFile.file_type, func.count(GeneratedFile.file_id)).group_by(GeneratedFile.file_type))
            file_types = {row[0]: row[1] for row in files_type_res.all()}
            total_files = (await db.execute(select(func.count(GeneratedFile.file_id)))).scalar() or 0
            total_storage = int((await db.execute(select(func.sum(GeneratedFile.file_size_bytes)))).scalar() or 0)
            total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
            total_sessions = (await db.execute(select(func.count(Session.session_id)))).scalar() or 0
            total_chunks = (await db.execute(select(func.count(DocumentChunk.chunk_id)))).scalar() or 0
            total_docs = (await db.execute(select(func.count(Document.doc_id)))).scalar() or 0
            total_mcp = (await db.execute(select(func.count(McpToolCall.call_id)))).scalar() or 0
            table_stats = [
                {"table": "document_chunks", "count": total_chunks},
                {"table": "documents", "count": total_docs},
                {"table": "pipeline_runs", "count": total_runs},
                {"table": "sessions", "count": total_sessions},
                {"table": "users", "count": total_users},
                {"table": "mcp_tool_calls", "count": total_mcp},
                {"table": "generated_files", "count": total_files},
            ]
            return {
                "total_runs": total_runs, "total_users": total_users, "total_sessions": total_sessions,
                "total_files": total_files, "total_storage_bytes": total_storage, "routes": routes,
                "route_metrics": route_metrics, "file_types": file_types,
                "avg_duration_ms": avg_duration, "table_stats": table_stats
            }

    async def get_runs(self, limit: int = 50, offset: int = 0, route_filter: str = None) -> list[dict]:
        async with get_session() as db:
            q = select(PipelineRun).order_by(desc(PipelineRun.created_at)).limit(limit).offset(offset)
            if route_filter: q = q.where(PipelineRun.routed_to == route_filter.upper())
            res = await db.execute(q)
            return [{"run_id": str(r.run_id), "original_query": r.original_query, "rewritten_query": r.rewritten_query, "routed_to": r.routed_to, "final_outcome": r.final_outcome, "retry_count": r.retry_count, "total_duration_ms": r.total_duration_ms, "created_at": r.created_at.isoformat() if r.created_at else None} for r in res.scalars().all()]

    async def get_run_steps(self, run_id: str) -> list[dict]:
        async with get_session() as db:
            res = await db.execute(select(PipelineStep).where(PipelineStep.run_id == uuid.UUID(str(run_id))).order_by(PipelineStep.step_order))
            return [{"step_id": s.step_id, "step_name": s.step_name, "step_order": s.step_order, "status": s.status, "duration_ms": s.duration_ms, "input_summary": s.input_summary, "output_summary": s.output_summary, "created_at": s.created_at.isoformat() if s.created_at else None} for s in res.scalars().all()]

    async def get_generated_files(self, limit: int = 50, offset: int = 0) -> list[dict]:
        async with get_session() as db:
            res = await db.execute(select(GeneratedFile).order_by(desc(GeneratedFile.created_at)).limit(limit).offset(offset))
            return [{"file_id": str(f.file_id), "file_name": f.file_name, "file_type": f.file_type, "storage_path": f.storage_path, "file_size_bytes": f.file_size_bytes, "created_at": f.created_at.isoformat() if f.created_at else None} for f in res.scalars().all()]

    async def delete_generated_file(self, file_id: str) -> Optional[dict]:
        async with get_session() as db:
            f = await db.get(GeneratedFile, uuid.UUID(str(file_id)))
            if f:
                record = {
                    "file_id": str(f.file_id),
                    "file_name": f.file_name,
                    "file_type": f.file_type,
                    "storage_path": f.storage_path,
                }
                await db.delete(f)
                await db.commit()
                return record
        return None

    async def get_mcp_calls(self, limit: int = 50, offset: int = 0) -> list[dict]:
        from src.database.models import McpToolCall
        from sqlalchemy.orm import joinedload
        async with get_session() as db:
            # The model has created_at / duration_ms / output_summary — not
            # started_at / completed_at / error_message. Referencing the latter
            # raised AttributeError on every call.
            res = await db.execute(
                select(McpToolCall)
                .options(joinedload(McpToolCall.run))
                .order_by(desc(McpToolCall.created_at))
                .limit(limit)
                .offset(offset)
            )
            return [{
                "call_id": str(c.call_id),
                "run_id": str(c.run_id),
                "mcp_server": c.mcp_server,
                "tool_name": c.tool_name,
                "input_params": c.input_params,
                "output_summary": c.output_summary,
                "status": c.status,
                "duration_ms": c.duration_ms,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "run": {"original_query": c.run.original_query} if c.run else None
            } for c in res.scalars().unique().all()]

    async def get_ingested_files_summary(self, project_id: str = None) -> list[dict]:
        async with get_session() as db:
            if project_id:
                from sqlalchemy import or_
                res = await db.execute(
                    select(Document)
                    .where(or_(Document.project_id == project_id, Document.is_global == True))
                    .order_by(desc(Document.ingested_at))
                )
            else:
                res = await db.execute(select(Document).order_by(desc(Document.ingested_at)))
            return [{"doc_id": str(d.doc_id), "filename": d.filename, "doc_type": d.doc_type, "chunk_count": d.chunk_count, "is_global": d.is_global, "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None} for d in res.scalars().all()]

    async def delete_ingested_file(self, doc_id: str) -> None:
        async with get_session() as db:
            res = await db.execute(select(Document).where(Document.doc_id == doc_id))
            d = res.scalars().first()
            if d:
                await db.delete(d)
                await db.commit()

    # ── Project Operations ──
    @staticmethod
    def _project_to_dict(p: Project) -> dict:
        return {
            "id": str(p.id), "user_id": str(p.user_id), "name": p.name,
            "description": p.description, "domain_context": p.domain_context,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }

    async def get_project(self, project_id: str) -> Optional[dict]:
        async with get_session() as db:
            res = await db.execute(select(Project).where(Project.id == uuid.UUID(str(project_id))))
            p = res.scalars().first()
            return self._project_to_dict(p) if p else None

    async def get_projects_for_user(self, user_id: str) -> list[dict]:
        async with get_session() as db:
            res = await db.execute(select(Project).where(Project.user_id == uuid.UUID(str(user_id))).order_by(desc(Project.created_at)))
            return [self._project_to_dict(p) for p in res.scalars().all()]

    async def create_project(self, data: dict) -> Optional[dict]:
        async with get_session() as db:
            p = Project(
                user_id=uuid.UUID(str(data["user_id"])),
                name=data["name"],
                description=data.get("description"),
                domain_context=data.get("domain_context"),
            )
            db.add(p)
            await db.commit()
            await db.refresh(p)
            return self._project_to_dict(p)

    async def update_project(self, project_id: str, data: dict) -> Optional[dict]:
        allowed = {"name", "description", "domain_context"}
        async with get_session() as db:
            res = await db.execute(select(Project).where(Project.id == uuid.UUID(str(project_id))))
            p = res.scalars().first()
            if not p:
                return None
            for k, v in data.items():
                if k in allowed:
                    setattr(p, k, v)
            await db.commit()
            await db.refresh(p)
            return self._project_to_dict(p)

    async def delete_project(self, project_id: str) -> None:
        async with get_session() as db:
            res = await db.execute(select(Project).where(Project.id == uuid.UUID(str(project_id))))
            p = res.scalars().first()
            if p:
                await db.delete(p)
                await db.commit()

    async def get_project_context(self, project_id: str) -> Optional[str]:
        async with get_session() as db:
            res = await db.execute(select(Project.domain_context).where(Project.id == uuid.UUID(str(project_id))))
            row = res.first()
            return row[0] if row else None

    async def get_project_memory(self, project_id: str) -> Optional[dict]:
        async with get_session() as db:
            res = await db.execute(select(ProjectMemory).where(ProjectMemory.project_id == uuid.UUID(str(project_id))).limit(1))
            m = res.scalars().first()
            return {"id": str(m.id), "project_id": str(m.project_id), "summary_text": m.summary_text} if m else None

    async def upsert_project_memory(self, project_id: str, summary_text: str) -> None:
        async with get_session() as db:
            res = await db.execute(select(ProjectMemory).where(ProjectMemory.project_id == uuid.UUID(str(project_id))).limit(1))
            m = res.scalars().first()
            if m:
                m.summary_text = summary_text
            else:
                db.add(ProjectMemory(project_id=uuid.UUID(str(project_id)), summary_text=summary_text))
            await db.commit()

    async def delete_document_records(self, source_file: str) -> None:
        async with get_session() as db:
            res = await db.execute(select(Document).where(Document.filename == source_file))
            for d in res.scalars().all():
                await db.delete(d)
            await db.commit()

    # ── Vector Store Operations ──
    async def insert_documents(self, documents: list[dict]) -> None:
        from sqlalchemy import text
        if not documents: return
        async with engine.begin() as conn:
            for doc in documents:
                if "project_id" not in doc:
                    doc["project_id"] = None
                await conn.execute(text("""
                    INSERT INTO documents (doc_id, filename, doc_type, is_global, project_id)
                    VALUES (:doc_id, :filename, :doc_type, :is_global, :project_id)
                    ON CONFLICT (doc_id) DO NOTHING
                """), doc)

    async def insert_chunks(self, chunks: list[dict]) -> None:
        import json as _json
        from sqlalchemy import text
        if not chunks: return
        batch_size = 500
        async with engine.begin() as conn:
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i+batch_size]
                # pgvector's `embedding` column takes its text form '[0.1,0.2,...]'.
                # asyncpg has no codec for a bare Python list here, so bind the
                # JSON string (pgvector parses it) — matching query_similar_chunks.
                # Without this, admin KB ingestion failed on the direct backend
                # with "expected str, got list".
                params = [
                    {**c, "embedding": _json.dumps(c["embedding"]) if isinstance(c.get("embedding"), (list, tuple)) else c.get("embedding")}
                    for c in batch
                ]
                await conn.execute(text("""
                    INSERT INTO document_chunks
                        (chunk_id, doc_id, chunk_index, chunk_text, embedding, fts_vector, source_file)
                    VALUES
                        (:chunk_id, :doc_id, :chunk_index, :chunk_text, :embedding, to_tsvector('english', :chunk_text), :source_file)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding,
                        fts_vector = EXCLUDED.fts_vector
                """), params)

    async def delete_chunks_by_source(self, source: str) -> int:
        async with get_session() as db:
            res = await db.execute(select(DocumentChunk).where(DocumentChunk.source_file == source))
            chunks = res.scalars().all()
            for c in chunks: await db.delete(c)
            await db.commit()
            return len(chunks)

    async def query_similar_chunks(self, query_text: str, query_embedding: list[float], top_k: int = 10, where: Optional[dict] = None) -> list[dict]:
        import json
        from sqlalchemy import text
        emb_str = json.dumps(query_embedding)
        # The :query_embedding param MUST be cast to ::vector. Bound as a bare
        # string, the HNSW index scan ran ~740ms; with the cast the planner uses
        # the index properly and it drops to ~1ms (whole query 1.9s -> 0.4s).
        sql = """
        WITH semantic_search AS (
            SELECT c.chunk_id, c.chunk_text, c.source_file, c.embedding <=> (:query_embedding)::vector AS distance,
                   ROW_NUMBER() OVER (ORDER BY c.embedding <=> (:query_embedding)::vector ASC) AS semantic_rank
            FROM document_chunks c
            LEFT JOIN documents d ON c.doc_id = d.doc_id
            {where_clause}
            ORDER BY distance ASC LIMIT :limit_k
        ),
        keyword_search AS (
            SELECT c.chunk_id, ts_rank(c.fts_vector, websearch_to_tsquery('english', :query_text)) AS bm25_score,
                   ROW_NUMBER() OVER (ORDER BY ts_rank(c.fts_vector, websearch_to_tsquery('english', :query_text)) DESC) AS keyword_rank
            FROM document_chunks c
            LEFT JOIN documents d ON c.doc_id = d.doc_id
            WHERE c.fts_vector @@ websearch_to_tsquery('english', :query_text) {and_where_clause}
            ORDER BY bm25_score DESC LIMIT :limit_k
        )
        SELECT 
            COALESCE(s.chunk_id, k.chunk_id) as id,
            COALESCE(s.chunk_text, (SELECT chunk_text FROM document_chunks WHERE chunk_id = k.chunk_id)) as text,
            COALESCE(s.source_file, (SELECT source_file FROM document_chunks WHERE chunk_id = k.chunk_id)) as source,
            COALESCE(1.0 / (60 + s.semantic_rank), 0.0) + COALESCE(1.0 / (60 + k.keyword_rank), 0.0) as rrf_score
        FROM semantic_search s FULL OUTER JOIN keyword_search k ON s.chunk_id = k.chunk_id
        ORDER BY rrf_score DESC LIMIT :top_k;
        """
        where_clause = ""
        and_where_clause = ""
        params = {"query_embedding": emb_str, "query_text": query_text, "limit_k": top_k * 2, "top_k": top_k}
        if where:
            conditions = []
            for k, v in where.items():
                if k == "source":
                    conditions.append("c.source_file = :source_file_param")
                    params["source_file_param"] = v
                elif k == "project_id":
                    conditions.append("(d.project_id = :project_id_param OR d.is_global = true)")
                    params["project_id_param"] = v
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)
                and_where_clause = "AND " + " AND ".join(conditions)

        results = []
        async with engine.begin() as conn:
            rows = await conn.execute(text(sql.format(where_clause=where_clause, and_where_clause=and_where_clause)), params)
            for row in rows: results.append({"id": row.id, "text": row.text, "metadata": {"source": row.source}, "rrf_score": float(row.rrf_score)})
        return results

    async def get_collection_count(self) -> int:
        async with get_session() as db:
            return (await db.execute(select(func.count(DocumentChunk.chunk_id)))).scalar() or 0

    async def query_tax_rates(self) -> str:
        return "DIRECT query_tax_rates not fully implemented."

    # ══════════════════════════════════════════════════════════════════
    # Admin dashboard: errors, ingestion jobs, KB stats, usage, latency
    # (mirrors RestGateway — both backends are live, so both must exist)
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _missing_table(exc: Exception) -> bool:
        return "does not exist" in str(exc).lower()

    @staticmethod
    def _naive_utc(since: str) -> datetime:
        """
        Parse an ISO cutoff to a naive UTC datetime.

        The pipeline_runs / pipeline_steps / error_logs timestamp columns are
        `timestamp without time zone`, so binding a tz-aware datetime raises
        "can't subtract offset-naive and offset-aware datetimes" under asyncpg.
        """
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    async def table_exists(self, table: str) -> bool:
        """Does this table/view exist? (See RestGateway.table_exists for why.)"""
        from sqlalchemy import text
        async with get_session() as db:
            res = await db.execute(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
            )
            return bool(res.scalar())

    async def log_error(self, record: dict) -> None:
        from src.database.models import ErrorLog
        try:
            async with get_session() as db:
                db.add(ErrorLog(
                    severity=record.get("severity", "error"),
                    error_type=record.get("error_type"),
                    module=record.get("module"),
                    message=record.get("message", ""),
                    stack_trace=record.get("stack_trace"),
                    run_id=uuid.UUID(record["run_id"]) if record.get("run_id") else None,
                    session_id=uuid.UUID(record["session_id"]) if record.get("session_id") else None,
                    user_id=uuid.UUID(record["user_id"]) if record.get("user_id") else None,
                    context=record.get("context"),
                ))
                await db.commit()
        except Exception:
            pass  # error logging must never raise

    async def get_errors(self, limit: int = 100, offset: int = 0, severity: str = None,
                         module: str = None, error_type: str = None,
                         since: str = None) -> list[dict]:
        from src.database.models import ErrorLog
        from datetime import datetime as _dt
        try:
            async with get_session() as db:
                q = select(ErrorLog)
                if severity:
                    q = q.where(ErrorLog.severity == severity)
                if module:
                    q = q.where(ErrorLog.module == module)
                if error_type:
                    q = q.where(ErrorLog.error_type == error_type)
                if since:
                    q = q.where(ErrorLog.occurred_at >= self._naive_utc(since))
                res = await db.execute(q.order_by(desc(ErrorLog.occurred_at)).limit(limit).offset(offset))
                return [{
                    "error_id": str(e.error_id),
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                    "severity": e.severity, "error_type": e.error_type, "module": e.module,
                    "message": e.message, "stack_trace": e.stack_trace,
                    "run_id": str(e.run_id) if e.run_id else None,
                    "session_id": str(e.session_id) if e.session_id else None,
                } for e in res.scalars().all()]
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    async def get_error_facets(self) -> dict:
        from src.database.models import ErrorLog
        try:
            async with get_session() as db:
                mods = (await db.execute(select(ErrorLog.module).distinct())).scalars().all()
                types = (await db.execute(select(ErrorLog.error_type).distinct())).scalars().all()
                sev = (await db.execute(select(ErrorLog.severity).distinct())).scalars().all()
                return {
                    "modules": sorted([m for m in mods if m]),
                    "error_types": sorted([t for t in types if t]),
                    "severities": sorted([s for s in sev if s]),
                }
        except Exception as exc:
            if self._missing_table(exc):
                return {"modules": [], "error_types": [], "severities": []}
            raise

    async def get_errors_since(self, since: str) -> list[dict]:
        from src.database.models import ErrorLog
        from datetime import datetime as _dt
        try:
            async with get_session() as db:
                res = await db.execute(
                    select(ErrorLog.occurred_at, ErrorLog.severity)
                    .where(ErrorLog.occurred_at >= self._naive_utc(since))
                )
                return [{"occurred_at": r[0].isoformat() if r[0] else None, "severity": r[1]} for r in res.all()]
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    # ── Ingestion jobs ──
    async def create_ingestion_job(self, data: dict) -> str:
        from src.database.models import IngestionJob
        job_id = uuid.uuid4()
        try:
            async with get_session() as db:
                db.add(IngestionJob(
                    job_id=job_id,
                    filename=data["filename"],
                    file_type=data.get("file_type"),
                    file_size_bytes=data.get("file_size_bytes"),
                    status=data.get("status", "processing"),
                    uploaded_by=uuid.UUID(data["uploaded_by"]) if data.get("uploaded_by") else None,
                ))
                await db.commit()
        except Exception as exc:
            if not self._missing_table(exc):
                raise
        return str(job_id)

    async def update_ingestion_job(self, job_id: str, data: dict) -> None:
        from src.database.models import IngestionJob
        from datetime import datetime as _dt
        allowed = {"doc_id", "status", "chunks_added", "error_message", "duration_ms"}
        try:
            async with get_session() as db:
                res = await db.execute(select(IngestionJob).where(IngestionJob.job_id == uuid.UUID(str(job_id))))
                job = res.scalars().first()
                if not job:
                    return
                for k, v in data.items():
                    if k in allowed:
                        setattr(job, k, v)
                if data.get("status") in ("success", "failed"):
                    job.finished_at = _dt.utcnow()
                await db.commit()
        except Exception as exc:
            if not self._missing_table(exc):
                raise

    async def get_ingestion_jobs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        from src.database.models import IngestionJob
        try:
            async with get_session() as db:
                res = await db.execute(
                    select(IngestionJob).order_by(desc(IngestionJob.started_at)).limit(limit).offset(offset)
                )
                return [{
                    "job_id": str(j.job_id), "doc_id": j.doc_id, "filename": j.filename,
                    "file_type": j.file_type, "file_size_bytes": j.file_size_bytes,
                    "status": j.status, "chunks_added": j.chunks_added,
                    "error_message": j.error_message, "duration_ms": j.duration_ms,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                } for j in res.scalars().all()]
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    # ── Knowledge base stats ──
    async def get_kb_stats(self) -> dict:
        """
        Chunks indexed, and how they are distributed across documents.

        Groups by source_file — the identity a human recognises and the one
        retrieval cites — rather than trusting documents.chunk_count, which is
        null for the existing corpus (see the chunker fix / migration 003).
        """
        async with get_session() as db:
            total_chunks = (await db.execute(select(func.count(DocumentChunk.chunk_id)))).scalar() or 0

            res = await db.execute(
                select(
                    DocumentChunk.source_file,
                    func.count(DocumentChunk.chunk_id).label("chunk_count"),
                    func.max(Document.doc_type).label("doc_type"),
                    func.max(Document.ingested_at).label("ingested_at"),
                )
                .select_from(DocumentChunk)
                .join(Document, Document.doc_id == DocumentChunk.doc_id, isouter=True)
                .group_by(DocumentChunk.source_file)
                .order_by(desc("chunk_count"))
                .limit(500)
            )
            docs = [{
                "doc_id": row.source_file,
                "filename": row.source_file,
                "doc_type": row.doc_type,
                "chunk_count": row.chunk_count or 0,
                "is_global": True,
                "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
            } for row in res.all()]

            return {
                "total_chunks": total_chunks,
                "total_documents": len(docs),
                "documents": docs,
                "grouped_by": "source_file",
            }

    # ── Usage / routing / latency ──
    async def get_runs_since(self, since: str) -> list[dict]:
        from datetime import datetime as _dt
        async with get_session() as db:
            res = await db.execute(
                select(PipelineRun).where(
                    PipelineRun.created_at >= self._naive_utc(since)
                )
            )
            return [{
                "run_id": str(r.run_id), "routed_to": r.routed_to, "final_outcome": r.final_outcome,
                "total_duration_ms": r.total_duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            } for r in res.scalars().all()]

    async def get_step_latencies_since(self, since: str) -> list[dict]:
        from datetime import datetime as _dt
        async with get_session() as db:
            res = await db.execute(
                select(PipelineStep.step_name, PipelineStep.duration_ms, PipelineStep.status,
                       PipelineStep.created_at)
                .where(PipelineStep.created_at >= self._naive_utc(since))
            )
            return [{
                "step_name": r[0], "duration_ms": r[1], "status": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
            } for r in res.all()]

    # ── Session attachments (per-conversation, NOT the knowledge base) ──
    @staticmethod
    def _attachment_to_dict(a, include_text: bool = False) -> dict:
        d = {
            "attachment_id": str(a.attachment_id), "session_id": str(a.session_id),
            "user_id": str(a.user_id) if a.user_id else None,
            "filename": a.filename, "file_type": a.file_type,
            "file_size_bytes": a.file_size_bytes, "char_count": a.char_count,
            "status": a.status, "error_message": a.error_message,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        if include_text:
            d["extracted_text"] = a.extracted_text
        return d

    async def create_attachment(self, data: dict) -> dict:
        from src.database.models import SessionAttachment
        async with get_session() as db:
            a = SessionAttachment(
                session_id=uuid.UUID(str(data["session_id"])),
                user_id=uuid.UUID(str(data["user_id"])) if data.get("user_id") else None,
                filename=data["filename"], file_type=data.get("file_type"),
                file_size_bytes=data.get("file_size_bytes"),
                extracted_text=data.get("extracted_text"),
                char_count=data.get("char_count"),
                status=data.get("status", "ready"),
                error_message=data.get("error_message"),
            )
            db.add(a)
            await db.commit()
            await db.refresh(a)
            return self._attachment_to_dict(a)

    async def get_attachments_for_session(self, session_id: str, include_text: bool = False) -> list[dict]:
        from src.database.models import SessionAttachment
        try:
            async with get_session() as db:
                res = await db.execute(
                    select(SessionAttachment)
                    .where(SessionAttachment.session_id == uuid.UUID(str(session_id)))
                    .order_by(SessionAttachment.created_at)
                )
                return [self._attachment_to_dict(a, include_text) for a in res.scalars().all()]
        except Exception as exc:
            if self._missing_table(exc):
                return []
            raise

    async def get_attachment(self, attachment_id: str) -> Optional[dict]:
        from src.database.models import SessionAttachment
        async with get_session() as db:
            res = await db.execute(
                select(SessionAttachment)
                .where(SessionAttachment.attachment_id == uuid.UUID(str(attachment_id)))
            )
            a = res.scalars().first()
            return self._attachment_to_dict(a, include_text=True) if a else None

    async def delete_attachment(self, attachment_id: str) -> None:
        from src.database.models import SessionAttachment
        async with get_session() as db:
            res = await db.execute(
                select(SessionAttachment)
                .where(SessionAttachment.attachment_id == uuid.UUID(str(attachment_id)))
            )
            a = res.scalars().first()
            if a:
                await db.delete(a)
                await db.commit()
