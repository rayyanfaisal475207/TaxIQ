# ============================================================
# Database Models — SQLAlchemy ORM (PostgreSQL)
#
# Defines all 7 tables from the TaxIQ Master Plan §3 using
# SQLAlchemy 2.0 declarative style (Mapped / mapped_column).
#
# TABLE OVERVIEW:
#
#   users                  — registered accounts
#   user_context_profiles  — per-user preferences (1:1 with users)
#   sessions               — chat sessions
#   messages               — individual chat messages within a session
#   pipeline_runs          — one row per orchestrator invocation
#   pipeline_steps         — ordered steps within a pipeline run
#   documents              — ingested knowledge-base documents
#
# POSTGRES-SPECIFIC TYPES USED:
#   UUID, JSONB, ARRAY(Text)
#
# NOTE: user_id is NULLABLE on sessions and pipeline_runs in
#       Phase 1. Phase 2 (auth) will add NOT NULL constraints
#       once every request carries an authenticated user.
# ============================================================

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Date,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ── Base Class ────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """
    Shared declarative base for all ORM models.
    Import this in Alembic's env.py so `Base.metadata` drives migrations.
    """
    pass


# ── Users ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    company_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plan: Mapped[str] = mapped_column(Text, default="free")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    # ── Relationships ──
    context_profile: Mapped[Optional["UserContextProfile"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False,
    )
    sessions: Mapped[List["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )
    projects: Mapped[List["Project"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )
    pipeline_runs: Mapped[List["PipelineRun"]] = relationship(
        back_populates="user",
    )
    documents: Mapped[List["Document"]] = relationship(
        back_populates="user",
    )
    generated_files: Mapped[List["GeneratedFile"]] = relationship(
        back_populates="user",
    )



# ── User Context Profiles ────────────────────────────────────────────────────

class UserContextProfile(Base):
    __tablename__ = "user_context_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    context_text: Mapped[str] = mapped_column(Text, default="")
    preferred_language: Mapped[str] = mapped_column(Text, default="english")
    llm_mode: Mapped[str] = mapped_column(Text, default="cloud")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(),
    )

    # ── Relationships ──
    user: Mapped["User"] = relationship(back_populates="context_profile")


# ── Projects (Phase 8 Support) ───────────────────────────────────────────────

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(),
    )

    # 🔗 Relationships 🔗
    user: Mapped["User"] = relationship(back_populates="projects")
    sessions: Mapped[List["Session"]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
    )


class ProjectMemory(Base):
    __tablename__ = "project_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
    )
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(),
    )


# ── Sessions ──────────────────────────────────────────────────────────────────

class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, default="New Chat")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True,
    )

    # ── Relationships ──
    user: Mapped[Optional["User"]] = relationship(back_populates="sessions")
    project: Mapped[Optional["Project"]] = relationship(back_populates="sessions")
    messages: Mapped[List["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
    )
    pipeline_runs: Mapped[List["PipelineRun"]] = relationship(
        back_populates="session",
    )
    generated_files: Mapped[List["GeneratedFile"]] = relationship(
        back_populates="session",
    )



# ── Messages ──────────────────────────────────────────────────────────────────

class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
    )
    citation_validated: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True,
    )
    unverified_citations: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(Text), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    # ── Relationships ──
    session: Mapped["Session"] = relationship(back_populates="messages")
    generated_files: Mapped[List["GeneratedFile"]] = relationship(
        back_populates="message",
    )



# ── Pipeline Runs ─────────────────────────────────────────────────────────────

class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id"),
        nullable=False,
    )
    # NULLABLE in Phase 1 — will become NOT NULL in Phase 2 (auth)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    original_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rewritten_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    routed_to: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    final_outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_duration_ms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    # ── Relationships ──
    session: Mapped["Session"] = relationship(back_populates="pipeline_runs")
    user: Mapped[Optional["User"]] = relationship(back_populates="pipeline_runs")
    steps: Mapped[List["PipelineStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan",
    )
    mcp_tool_calls: Mapped[List["McpToolCall"]] = relationship(
        back_populates="run", cascade="all, delete-orphan",
    )



# ── Pipeline Steps ────────────────────────────────────────────────────────────

class PipelineStep(Base):
    __tablename__ = "pipeline_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'skipped', 'retry', 'failed')",
            name="ck_pipeline_steps_status",
        ),
    )

    step_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.run_id"),
        nullable=False,
    )
    step_name: Mapped[str] = mapped_column(Text, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    # ── Relationships ──
    run: Mapped["PipelineRun"] = relationship(back_populates="steps")


# ── Documents ─────────────────────────────────────────────────────────────────

class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )
    document_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    effective_from: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    effective_to: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Relationships ──
    user: Mapped[Optional["User"]] = relationship(back_populates="documents")
    project: Mapped[Optional["Project"]] = relationship()
    chunks: Mapped[List["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan",
    )


# ── Document Chunks (pgvector) ────────────────────────────────────────────────
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[str] = mapped_column(Text, primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        Text, ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    
    # 384 dimensions for local all-MiniLM-L6-v2 embeddings (used in ChromaDB)
    # If switching to OpenAI/Gemini later, we'd need a migration to alter the column type
    embedding: Mapped[Optional[Vector]] = mapped_column(Vector(384), nullable=True)
    
    # Full-text search vector
    fts_vector: Mapped[Optional[str]] = mapped_column(TSVECTOR, nullable=True)

    # Metadata for filtering
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    effective_to: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="chunks")


# ── Tax Rates (Phase 5B) ──────────────────────────────────────────────────────
from sqlalchemy import Numeric, Date

class TaxRate(Base):
    __tablename__ = "tax_rates"

    rate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tax_type: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    filer_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rate_percent: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 2), nullable=True,
    )
    fixed_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 2), nullable=True,
    )
    effective_from: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    section_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_document: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )


# ── MCP Tool Calls (Phase 5B) ─────────────────────────────────────────────────

class McpToolCall(Base):
    __tablename__ = "mcp_tool_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="ck_mcp_calls_status",
        ),
    )

    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_runs.run_id"),
        nullable=False,
    )
    mcp_server: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    input_params: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rejected_by_role: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )

    # ── Relationships ──
    run: Mapped["PipelineRun"] = relationship(back_populates="mcp_tool_calls")


# ── Generated Files (Phase 6B) ────────────────────────────────────────────────

class GeneratedFile(Base):
    __tablename__ = "generated_files"

    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.message_id"),
        nullable=True,
    )
    file_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ── Relationships ──
    session: Mapped["Session"] = relationship(back_populates="generated_files")
    user: Mapped["User"] = relationship(back_populates="generated_files")
    message: Mapped[Optional["Message"]] = relationship(back_populates="generated_files")





# ── Observability: Error Log (Migration 003) ──────────────────────────────────

class ErrorLog(Base):
    """
    Every ERROR/CRITICAL the backend emits, persisted so the admin dashboard
    can show error history and trends. Previously the only record of a failure
    was a line in a terminal.
    """
    __tablename__ = "error_logs"

    error_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    severity: Mapped[str] = mapped_column(Text, default="error")   # warning | error | critical
    error_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    module: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    context: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)


# ── Ingestion Jobs (Migration 003) ────────────────────────────────────────────

class IngestionJob(Base):
    """
    One row per admin knowledge-base upload, so the dashboard can report
    processing / success / failed per document — and *why* it failed.
    """
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    doc_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="processing")  # processing|success|failed
    chunks_added: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


# ── Session Attachments (Migration 003) ───────────────────────────────────────

class SessionAttachment(Base):
    """
    A file attached to ONE conversation from the chat composer.

    Deliberately NOT part of the knowledge base: the text is extracted once and
    injected into that conversation's prompt. These rows never reach
    `document_chunks`, are never embedded, and are never retrievable from
    another conversation. The two systems are separate by construction.
    """
    __tablename__ = "session_attachments"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    char_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="ready")   # ready | failed
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
